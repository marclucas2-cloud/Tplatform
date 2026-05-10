import sys
import json
import sqlite3
from datetime import UTC, datetime, timedelta
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


def test_strategy_pnl_5d_aggregates_db_jsonl_and_open_positions(tmp_path, monkeypatch):
    import dashboard_data

    data_dir = tmp_path / "data"
    state_dir = data_dir / "state"
    state_dir.mkdir(parents=True)
    monkeypatch.setattr(dashboard_data, "DATA_DIR", data_dir)
    monkeypatch.setattr(dashboard_data, "STATE_DIR", state_dir)

    db = data_dir / "live_journal.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE trades (
            trade_id TEXT PRIMARY KEY,
            strategy TEXT,
            instrument TEXT,
            direction TEXT,
            quantity REAL,
            entry_price REAL,
            exit_price REAL,
            entry_time TEXT,
            exit_time TEXT,
            pnl_net REAL,
            status TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "T1",
            "Cross-Asset Mom",
            "MNQ",
            "BUY",
            1,
            100,
            110,
            (datetime.now(UTC) - timedelta(days=2)).isoformat(),
            (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            123.45,
            "closed",
        ),
    )
    conn.commit()
    conn.close()

    journal_dir = state_dir / "alt_rel_strength"
    journal_dir.mkdir()
    before = {"as_of_date": (datetime.now(UTC) - timedelta(days=6)).isoformat(), "cumulative_pnl_usd": 10.0}
    after = {"as_of_date": (datetime.now(UTC) - timedelta(days=1)).isoformat(), "cumulative_pnl_usd": -5.0}
    journal_dir.joinpath("paper_journal.jsonl").write_text(
        json.dumps(before) + "\n" + json.dumps(after) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        dashboard_data,
        "get_ibkr_positions_via_insync",
        lambda *args, **kwargs: [{"strategy": "Gold-Oil Rotation", "pnl": 77.0, "source": "test"}]
        if kwargs.get("mode") == "live"
        else [],
    )
    monkeypatch.setattr(dashboard_data, "get_binance_positions", lambda: [])
    monkeypatch.setattr(dashboard_data, "get_alpaca_positions", lambda: [])

    pnl = dashboard_data._strategy_pnl_5d()

    assert pnl["cross_asset_momentum"]["pnl"] == 123.45
    assert pnl["alt_rel_strength_14_60_7"]["pnl"] == -15.0
    assert pnl["gold_oil_rotation"]["pnl"] == 77.0
