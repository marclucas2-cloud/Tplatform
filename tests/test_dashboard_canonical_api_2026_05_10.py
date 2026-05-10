import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "dashboard" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_dashboard_strategies_use_quant_registry():
    from dashboard_data import build_strategy_rows, load_quant_registry

    registry = load_quant_registry()
    rows = build_strategy_rows()
    ids = {row["id"] for row in rows}
    canonical_ids = {row["strategy_id"] for row in registry["strategies"]}

    assert ids == canonical_ids
    assert "cross_asset_momentum" in ids
    assert "gold_oil_rotation" in ids
    assert "btc_asia_mes_leadlag_q80_v80_long_only" in ids
    assert "btc_eth_dual_momentum" not in ids


def test_dashboard_strategy_rows_include_columns_needed_by_frontend():
    from dashboard_data import build_strategy_rows

    rows = build_strategy_rows()
    required = {
        "id",
        "phase",
        "asset_class",
        "broker",
        "sharpe",
        "allocation_pct",
        "pnl_5d",
        "kill_switch_status",
    }
    for row in rows:
        assert required <= set(row)

    live_rows = [row for row in rows if row["phase"] in {"LIVE", "LIVE_MICRO"}]
    assert live_rows
    assert any(row["allocation_pct"] is not None for row in live_rows)


def test_dashboard_spa_catchall_is_registered_after_late_api_routes():
    import main

    route_paths = [getattr(route, "path", "") for route in main.app.routes]
    catchall_index = route_paths.index("/{full_path:path}")
    for api_path in [
        "/api/futures/positions",
        "/api/futures/trades",
        "/api/governance/live-whitelist",
        "/api/equity-history",
    ]:
        assert route_paths.index(api_path) < catchall_index


def test_live_drawdown_ignores_stale_broker_anchor(monkeypatch):
    import dashboard_data

    monkeypatch.setattr(
        dashboard_data,
        "live_equity_total",
        lambda: {
            "ibkr_equity": 29_000.0,
            "binance_equity": 10_000.0,
            "alpaca_live_equity": 0.0,
            "live_equity": 39_000.0,
        },
    )
    monkeypatch.setattr(
        dashboard_data,
        "load_live_risk_dd_state",
        lambda: {"daily_start_equity": 29_000.0, "peak_equity": 29_000.0},
    )
    monkeypatch.setattr(
        dashboard_data,
        "load_crypto_dd_state",
        lambda: {"daily_start_equity": 5_600.0, "peak_equity": 5_600.0},
    )

    snap = dashboard_data.live_drawdown_snapshot()

    assert snap["daily_start"] == 39_000.0
    assert snap["daily_pnl_pct"] == 0.0
    assert snap["current_pct"] == 0.0
