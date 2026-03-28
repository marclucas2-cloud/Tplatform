"""
Live Trading Dashboard API -- FastAPI endpoints for live monitoring.

Serves data to the React dashboard for the LIVE tab.
All endpoints are read-only (no trading actions via API).

Endpoints:
  GET /api/live/overview      -- P&L, positions, margin, system status
  GET /api/live/positions      -- Detailed open positions
  GET /api/live/pnl            -- P&L breakdown (today, MTD, YTD)
  GET /api/live/execution      -- Slippage and cost metrics
  GET /api/live/risk           -- VaR, drawdown, kill switch status
  GET /api/live/kpi            -- Scaling KPI progress
  GET /api/live/trades         -- Recent trade history
  GET /api/live/alerts         -- Recent alerts
  GET /api/live/comparison     -- Live vs Paper comparison
  GET /api/live/health         -- System health check
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# Since FastAPI may not be installed in all environments,
# we create a router factory that works with or without FastAPI

def create_live_router(
    trade_journal=None,
    slippage_tracker=None,
    cost_tracker=None,
    var_calculator=None,
    risk_manager=None,
    leverage_manager=None,
    kill_switch=None,
    reconciliation=None,
    broker=None,
    paper_journal=None,
):
    """Create FastAPI router with live trading endpoints.

    All dependencies are injected -- the router doesn't import
    any trading modules directly. This allows testing with mocks.

    Args:
        trade_journal: TradeJournal instance (mode=LIVE)
        slippage_tracker: SlippageTracker instance
        cost_tracker: CostTracker instance
        var_calculator: LiveVaRCalculator instance
        risk_manager: LiveRiskManager instance
        leverage_manager: LeverageManager instance
        kill_switch: LiveKillSwitch instance
        reconciliation: LiveReconciliation instance
        broker: BaseBroker instance (live)
        paper_journal: TradeJournal instance (mode=PAPER) for comparison
    """
    try:
        from fastapi import APIRouter, Query, HTTPException
    except ImportError:
        # Return a dummy router if FastAPI not installed
        logger.warning("FastAPI not installed -- live endpoints not available")
        return None

    router = APIRouter(prefix="/api/live", tags=["live"])

    # ------------------------------------------------------------------
    # GET /api/live/overview
    # ------------------------------------------------------------------
    @router.get("/overview")
    def get_overview():
        """Main overview: P&L, positions, margin, status.

        Returns: {
            timestamp, mode,
            pnl_today, pnl_mtd, pnl_ytd,
            positions_count, margin_used_pct,
            leverage_max, phase,
            kill_switch_active, strategies_active, strategies_paused,
            system_status: "OK" | "WARNING" | "CRITICAL"
        }
        """
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "LIVE",
        }

        # P&L from trade journal (get_pnl returns dict)
        if trade_journal:
            try:
                pnl_today = trade_journal.get_pnl("today")
                result["pnl_today"] = pnl_today.get("pnl_net", 0)
                result["pnl_today_gross"] = pnl_today.get("pnl_gross", 0)
                result["trades_today"] = pnl_today.get("n_trades", 0)
                result["win_rate_today"] = pnl_today.get("win_rate", 0)
            except Exception as e:
                logger.error("Failed to get today P&L: %s", e)
                result["pnl_today"] = 0

            try:
                pnl_mtd = trade_journal.get_pnl("mtd")
                result["pnl_mtd"] = pnl_mtd.get("pnl_net", 0)
            except Exception as e:
                logger.error("Failed to get MTD P&L: %s", e)
                result["pnl_mtd"] = 0

            try:
                pnl_ytd = trade_journal.get_pnl("ytd")
                result["pnl_ytd"] = pnl_ytd.get("pnl_net", 0)
            except Exception as e:
                logger.error("Failed to get YTD P&L: %s", e)
                result["pnl_ytd"] = 0

        # Positions from broker
        if broker:
            try:
                positions = broker.get_positions()
                result["positions_count"] = len(positions)
                account = broker.get_account_info()
                equity = account.get("equity", 1)
                margin_used = account.get("margin_used", 0)
                result["equity"] = equity
                result["margin_used_pct"] = round(
                    margin_used / max(equity, 1), 4
                )
            except Exception as e:
                logger.error("Broker error in overview: %s", e)
                result["positions_count"] = -1
                result["broker_error"] = str(e)

        # Leverage
        if leverage_manager:
            try:
                status = leverage_manager.get_status()
                result["leverage_max"] = status.get("max_leverage", 1.5)
                result["phase"] = status.get("current_phase", "PHASE_1")
                result["days_in_phase"] = status.get("days_in_phase", 0)
            except Exception as e:
                logger.error("Leverage manager error: %s", e)

        # Kill switch
        if kill_switch:
            result["kill_switch_active"] = kill_switch.is_active

        # System status
        result["system_status"] = _determine_system_status(result)

        return result

    # ------------------------------------------------------------------
    # GET /api/live/positions
    # ------------------------------------------------------------------
    @router.get("/positions")
    def get_positions():
        """Detailed open positions.

        Returns: {positions: [{
            symbol, direction, quantity, avg_entry,
            current_price, unrealized_pnl, unrealized_pnl_pct,
            strategy, has_bracket, entry_time
        }], count: int}
        """
        if not broker:
            return {"positions": [], "count": 0, "error": "Broker not connected"}
        try:
            positions = broker.get_positions()

            enriched = []
            # Enrich with journal data if available
            open_trades_by_symbol = {}
            if trade_journal:
                try:
                    open_trades = trade_journal.get_open_trades()
                    for t in open_trades:
                        sym = t.get("instrument", "")
                        open_trades_by_symbol[sym] = t
                except Exception:
                    pass

            for p in positions:
                symbol = p.get("symbol", "")
                trade_info = open_trades_by_symbol.get(symbol, {})
                qty = float(p.get("qty", 0))
                enriched.append({
                    "symbol": symbol,
                    "direction": "LONG" if qty > 0 else "SHORT",
                    "quantity": abs(qty),
                    "avg_entry": float(p.get("avg_entry", 0)),
                    "current_price": float(p.get("current_price", 0)),
                    "unrealized_pnl": float(p.get("unrealized_pl", 0)),
                    "unrealized_pnl_pct": float(p.get("unrealized_plpc", 0)) * 100,
                    "market_value": float(p.get("market_val", 0)),
                    "strategy": trade_info.get("strategy", "unknown"),
                    "has_bracket": bool(
                        trade_info.get("stop_loss") or trade_info.get("take_profit")
                    ),
                    "entry_time": trade_info.get("timestamp_filled"),
                })

            return {"positions": enriched, "count": len(enriched)}
        except Exception as e:
            raise HTTPException(status_code=503, detail=str(e))

    # ------------------------------------------------------------------
    # GET /api/live/pnl
    # ------------------------------------------------------------------
    @router.get("/pnl")
    def get_pnl(period: str = Query("today", pattern="^(today|7d|30d|mtd|ytd)$")):
        """P&L for a specific period.

        Returns: {period, pnl_net, pnl_gross, total_commission, n_trades,
                  win_rate, daily_summary (if period=today)}
        """
        if not trade_journal:
            return {"period": period, "pnl_net": 0, "error": "Trade journal not configured"}

        try:
            pnl_data = trade_journal.get_pnl(period)
            result = {
                "period": period,
                "pnl_net": pnl_data.get("pnl_net", 0),
                "pnl_gross": pnl_data.get("pnl_gross", 0),
                "total_commission": pnl_data.get("total_commission", 0),
                "n_trades": pnl_data.get("n_trades", 0),
                "win_rate": pnl_data.get("win_rate", 0),
            }
            # Include daily summary for today
            if period == "today":
                try:
                    result["daily_summary"] = trade_journal.get_daily_summary()
                except Exception:
                    result["daily_summary"] = None
            return result
        except Exception as e:
            return {"period": period, "pnl_net": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # GET /api/live/execution
    # ------------------------------------------------------------------
    @router.get("/execution")
    def get_execution(period: str = Query("7d")):
        """Execution quality: slippage and costs.

        Returns: {
            slippage: {by_strategy, by_instrument_type, worst_trades, ...},
            costs: {total_commission, cost_ratio, by_strategy, ...},
        }
        """
        result = {"period": period}
        if slippage_tracker:
            try:
                result["slippage"] = slippage_tracker.get_summary(period=period)
            except Exception as e:
                logger.error("Slippage tracker error: %s", e)
                result["slippage"] = {"error": str(e)}
        if cost_tracker:
            try:
                result["costs"] = cost_tracker.get_cost_report(period=period)
            except Exception as e:
                logger.error("Cost tracker error: %s", e)
                result["costs"] = {"error": str(e)}
        return result

    # ------------------------------------------------------------------
    # GET /api/live/risk
    # ------------------------------------------------------------------
    @router.get("/risk")
    def get_risk():
        """Risk metrics: VaR, drawdown, kill switch, reconciliation.

        Returns: {
            var_history: [...],
            kill_switch: {is_active, is_armed, thresholds, ...},
            reconciliation: {total_runs, divergence_rate, ...},
        }
        """
        result = {}
        if var_calculator:
            try:
                result["var_history"] = var_calculator.get_var_history(days=7)
            except Exception as e:
                logger.error("VaR calculator error: %s", e)
                result["var_history"] = {"error": str(e)}
        if kill_switch:
            try:
                result["kill_switch"] = kill_switch.get_status()
            except Exception as e:
                logger.error("Kill switch status error: %s", e)
                result["kill_switch"] = {"error": str(e)}
        if reconciliation:
            try:
                result["reconciliation"] = reconciliation.get_stats()
            except Exception as e:
                logger.error("Reconciliation stats error: %s", e)
                result["reconciliation"] = {"error": str(e)}
        return result

    # ------------------------------------------------------------------
    # GET /api/live/kpi
    # ------------------------------------------------------------------
    @router.get("/kpi")
    def get_kpi():
        """Scaling KPI progress.

        Returns: {
            gate, conditions: [{name, threshold, current, passed}],
            leverage: {current_phase, max_leverage, ...},
        }
        """
        result = {"gate": "M1", "conditions": []}

        if trade_journal:
            try:
                pnl_30d = trade_journal.get_pnl("30d")
                trades = trade_journal.get_trades(status="CLOSED", limit=1000)

                result["conditions"].append({
                    "name": "min_trades",
                    "threshold": 50,
                    "current": len(trades),
                    "passed": len(trades) >= 50,
                })
                result["conditions"].append({
                    "name": "pnl_30d_positive",
                    "threshold": 0,
                    "current": pnl_30d.get("pnl_net", 0),
                    "passed": pnl_30d.get("pnl_net", 0) > 0,
                })
                result["conditions"].append({
                    "name": "win_rate_above_45",
                    "threshold": 45.0,
                    "current": pnl_30d.get("win_rate", 0),
                    "passed": pnl_30d.get("win_rate", 0) >= 45.0,
                })
            except Exception as e:
                logger.error("KPI trade journal error: %s", e)

        if leverage_manager:
            try:
                result["leverage"] = leverage_manager.get_status()
            except Exception as e:
                logger.error("KPI leverage error: %s", e)

        if kill_switch:
            try:
                ks_status = kill_switch.get_status()
                result["conditions"].append({
                    "name": "no_kill_switch_activations",
                    "threshold": 0,
                    "current": ks_status.get("total_activations", 0),
                    "passed": ks_status.get("total_activations", 0) == 0,
                })
            except Exception:
                pass

        # Overall pass/fail
        if result["conditions"]:
            all_passed = all(c["passed"] for c in result["conditions"])
            result["overall"] = "PASS" if all_passed else "FAIL"
        else:
            result["overall"] = "PENDING"

        return result

    # ------------------------------------------------------------------
    # GET /api/live/trades
    # ------------------------------------------------------------------
    @router.get("/trades")
    def get_trades(
        limit: int = Query(50, ge=1, le=500),
        strategy: Optional[str] = None,
        status: Optional[str] = None,
    ):
        """Recent trade history."""
        if not trade_journal:
            return {"trades": [], "count": 0}
        try:
            trades = trade_journal.get_trades(
                strategy=strategy, status=status, limit=limit
            )
            return {"trades": trades, "count": len(trades)}
        except Exception as e:
            return {"trades": [], "count": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # GET /api/live/alerts
    # ------------------------------------------------------------------
    @router.get("/alerts")
    def get_alerts(
        limit: int = Query(50, ge=1, le=200),
        level: Optional[str] = None,
    ):
        """Recent alerts from kill switch and slippage tracker."""
        alerts = []

        # Kill switch history contains activation/deactivation events
        if kill_switch:
            try:
                history = kill_switch.get_history()
                for event in history[-limit:]:
                    alerts.append({
                        "source": "kill_switch",
                        "level": "critical" if event.get("action") == "ACTIVATE" else "info",
                        "message": event.get("reason", ""),
                        "timestamp": event.get("timestamp", ""),
                        "action": event.get("action", ""),
                    })
            except Exception:
                pass

        # Slippage alerts
        if slippage_tracker:
            try:
                slip_alerts = slippage_tracker.check_alerts()
                for sa in slip_alerts:
                    alerts.append({
                        "source": "slippage",
                        "level": sa.get("level", "warning"),
                        "message": (
                            f"{sa['strategy']}: avg slippage {sa['avg_slippage_bps']:.1f} bps "
                            f"({sa['avg_ratio']:.1f}x backtest)"
                        ),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "strategy": sa.get("strategy"),
                    })
            except Exception:
                pass

        # Filter by level if requested
        if level:
            alerts = [a for a in alerts if a.get("level") == level]

        # Sort by timestamp descending
        alerts.sort(key=lambda a: a.get("timestamp", ""), reverse=True)

        return {"alerts": alerts[:limit], "count": len(alerts)}

    # ------------------------------------------------------------------
    # GET /api/live/comparison
    # ------------------------------------------------------------------
    @router.get("/comparison")
    def get_comparison():
        """Live vs Paper comparison for same strategies."""
        result = {"comparison": [], "periods": ["today", "7d", "30d"]}

        if not trade_journal or not paper_journal:
            result["error"] = "Both live and paper journals required"
            return result

        for period in ["today", "7d", "30d"]:
            try:
                live_pnl = trade_journal.get_pnl(period)
                paper_pnl = paper_journal.get_pnl(period)
                result["comparison"].append({
                    "period": period,
                    "live_pnl_net": live_pnl.get("pnl_net", 0),
                    "live_trades": live_pnl.get("n_trades", 0),
                    "live_win_rate": live_pnl.get("win_rate", 0),
                    "paper_pnl_net": paper_pnl.get("pnl_net", 0),
                    "paper_trades": paper_pnl.get("n_trades", 0),
                    "paper_win_rate": paper_pnl.get("win_rate", 0),
                    "divergence": round(
                        live_pnl.get("pnl_net", 0) - paper_pnl.get("pnl_net", 0), 2
                    ),
                })
            except Exception as e:
                logger.error("Comparison error for %s: %s", period, e)

        return result

    # ------------------------------------------------------------------
    # GET /api/live/health
    # ------------------------------------------------------------------
    @router.get("/health")
    def get_health():
        """System health check for all live components."""
        health = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {},
        }

        # Broker connectivity
        if broker:
            try:
                broker.get_account_info()
                health["components"]["broker"] = {"status": "OK"}
            except Exception as e:
                health["components"]["broker"] = {
                    "status": "ERROR",
                    "error": str(e),
                }

        # Kill switch
        if kill_switch:
            try:
                health["components"]["kill_switch"] = {
                    "status": "ACTIVE" if kill_switch.is_active else "ARMED",
                    "armed": kill_switch.is_armed,
                }
            except Exception as e:
                health["components"]["kill_switch"] = {
                    "status": "ERROR",
                    "error": str(e),
                }

        # Reconciliation
        if reconciliation:
            try:
                stats = reconciliation.get_stats()
                div_rate = stats.get("divergence_rate", 0)
                health["components"]["reconciliation"] = {
                    "status": "OK" if div_rate < 0.05 else "WARNING",
                    "divergence_rate": div_rate,
                    "total_runs": stats.get("total_runs", 0),
                    "last_run": stats.get("last_run"),
                }
            except Exception as e:
                health["components"]["reconciliation"] = {
                    "status": "ERROR",
                    "error": str(e),
                }

        # Trade journal
        if trade_journal:
            try:
                trade_journal.get_pnl("today")
                health["components"]["trade_journal"] = {"status": "OK"}
            except Exception as e:
                health["components"]["trade_journal"] = {
                    "status": "ERROR",
                    "error": str(e),
                }

        # VaR calculator
        if var_calculator:
            try:
                var_calculator.get_var_history(days=1)
                health["components"]["var_calculator"] = {"status": "OK"}
            except Exception as e:
                health["components"]["var_calculator"] = {
                    "status": "ERROR",
                    "error": str(e),
                }

        # Leverage manager
        if leverage_manager:
            try:
                leverage_manager.get_status()
                health["components"]["leverage_manager"] = {"status": "OK"}
            except Exception as e:
                health["components"]["leverage_manager"] = {
                    "status": "ERROR",
                    "error": str(e),
                }

        # Overall status
        statuses = [c.get("status") for c in health["components"].values()]
        if any(s == "ERROR" for s in statuses):
            health["overall"] = "DEGRADED"
        elif any(s == "WARNING" for s in statuses):
            health["overall"] = "WARNING"
        elif any(s == "ACTIVE" for s in statuses):
            # Kill switch ACTIVE = critical
            health["overall"] = "CRITICAL"
        elif not statuses:
            health["overall"] = "NO_COMPONENTS"
        else:
            health["overall"] = "OK"

        return health

    # ------------------------------------------------------------------
    # Helpers (closure, accessible to all endpoints)
    # ------------------------------------------------------------------
    def _determine_system_status(data):
        """Determine overall system status from overview data."""
        if data.get("kill_switch_active"):
            return "CRITICAL"
        margin = data.get("margin_used_pct", 0)
        if margin > 0.85:
            return "CRITICAL"
        if margin > 0.70:
            return "WARNING"
        if data.get("broker_error"):
            return "WARNING"
        return "OK"

    return router
