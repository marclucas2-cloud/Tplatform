from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_ibkr_import_error_is_fail_closed():
    from core.broker.base import BrokerError
    from core.broker.ibkr_adapter import IBKRBroker

    broker = IBKRBroker.__new__(IBKRBroker)
    broker._ib = MagicMock()
    broker._paper = False
    broker._host = "127.0.0.1"
    broker._port = 4002
    broker._client_id = 1
    broker._connected = False
    broker._permanently_down = False
    broker._reconnect_attempts = 0

    real_import = __import__

    def _guarded_import(name, *args, **kwargs):
        if name == "core.governance.pre_order_guard":
            raise ImportError("guard missing")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_guarded_import):
        try:
            broker.create_position("MES", "BUY", qty=1, _authorized_by="cross_asset_momentum")
        except BrokerError as e:
            assert "fail-closed" in str(e).lower() or "unavailable" in str(e).lower()
        else:
            raise AssertionError("IBKR create_position should fail-closed on ImportError")


def test_live_portfolio_eu_migrates_legacy_state(tmp_path):
    from scripts import live_portfolio_eu as eu

    legacy = tmp_path / "paper_portfolio_eu_state.json"
    canonical = tmp_path / "data" / "state" / "ibkr_eu" / "portfolio_state.json"
    payload = {
        "capital": 12345.0,
        "positions": {},
        "allocations": {},
        "daily_capital_start": 12345.0,
        "daily_pnl": 0.0,
        "last_run_date": None,
        "history": [],
        "intraday_positions": {},
        "strategy_pnl_log": {},
    }
    legacy.write_text(json.dumps(payload), encoding="utf-8")

    with patch.object(eu, "STATE_FILE", canonical), patch.object(eu, "LEGACY_STATE_FILES", [legacy]):
        state = eu.load_state()

    assert state["capital"] == 12345.0
    assert canonical.exists()
    migrated = json.loads(canonical.read_text(encoding="utf-8"))
    assert migrated["capital"] == 12345.0


def test_book_health_ibkr_futures_accepts_canonical_paths(tmp_path, monkeypatch):
    from core.governance import book_health
    from core.governance import data_freshness

    (tmp_path / "data" / "futures").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "state" / "ibkr_futures").mkdir(parents=True, exist_ok=True)

    # Match data_freshness.FRESHNESS_REQUIREMENTS["ibkr_futures"] (post-2026-04-18:
    # bascule de *_LONG.parquet fantomes vers *_1D.parquet reels)
    for name in ("MES_1D.parquet", "MGC_1D.parquet", "MCL_1D.parquet"):
        (tmp_path / "data" / "futures" / name).write_text("ok", encoding="utf-8")

    (tmp_path / "data" / "state" / "ibkr_futures" / "positions_live.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "state" / "ibkr_futures" / "equity_state.json").write_text(
        json.dumps({"equity": 10000}),
        encoding="utf-8",
    )

    monkeypatch.setattr(book_health, "ROOT", tmp_path)
    monkeypatch.setattr(data_freshness, "ROOT", tmp_path)
    monkeypatch.setattr(
        book_health,
        "_whitelist_integrity_check",
        lambda _book: book_health.HealthCheck("whitelist_integrity", book_health.HealthStatus.GREEN, "ok", 2),
    )
    monkeypatch.setattr(
        book_health,
        "_tcp_connect_check",
        lambda *args, **kwargs: book_health.HealthCheck(
            "ibkr_gateway", book_health.HealthStatus.GREEN, "up"
        ),
    )
    monkeypatch.setattr(
        book_health,
        "_quick_ibkr_snapshot",
        lambda *args, **kwargs: (
            {"equity": 10000.0, "cash": 5000.0, "buying_power": 20000.0},
            [],
            None,
        ),
    )

    health = book_health.check_ibkr_futures()

    assert health.status == book_health.HealthStatus.GREEN
    assert any(c.name == "futures_state" and c.status == book_health.HealthStatus.GREEN for c in health.checks)
    assert any(c.name == "ibkr_equity" and c.status == book_health.HealthStatus.GREEN for c in health.checks)


def test_book_health_ibkr_futures_blocks_fast_when_gateway_unreachable(tmp_path, monkeypatch):
    from core.governance import book_health

    calls = {"snapshot": 0}

    monkeypatch.setattr(book_health, "ROOT", tmp_path)
    monkeypatch.setattr(
        book_health,
        "_whitelist_integrity_check",
        lambda _book: book_health.HealthCheck("whitelist_integrity", book_health.HealthStatus.GREEN, "ok", 2),
    )
    monkeypatch.setattr(
        book_health,
        "_tcp_connect_check",
        lambda *args, **kwargs: book_health.HealthCheck(
            "ibkr_gateway", book_health.HealthStatus.BLOCKED, "down"
        ),
    )

    def _unexpected_snapshot(*args, **kwargs):
        calls["snapshot"] += 1
        return None, None, "should not be called"

    monkeypatch.setattr(book_health, "_quick_ibkr_snapshot", _unexpected_snapshot)

    health = book_health.check_ibkr_futures()

    assert health.status == book_health.HealthStatus.BLOCKED
    assert calls["snapshot"] == 0
    assert any(c.name == "ibkr_gateway" and c.status == book_health.HealthStatus.BLOCKED for c in health.checks)
    assert any(c.name == "ibkr_account" and c.status == book_health.HealthStatus.BLOCKED for c in health.checks)


def test_alpaca_auth_persists_equity_state(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from core.alpaca_client import client as alp_client

    monkeypatch.setattr(alp_client, "ROOT", tmp_path)

    client = alp_client.AlpacaClient(api_key="k", secret_key="s", paper=True)
    mock_account = SimpleNamespace(
        status="ACTIVE",
        equity="1000.0",
        cash="400.0",
        buying_power="1400.0",
        currency="USD",
        account_number="PA123",
    )
    client._trading = MagicMock()
    client._trading.get_account.return_value = mock_account

    result = client.authenticate()

    state_path = tmp_path / "data" / "state" / "alpaca_us" / "equity_state.json"
    assert result["equity"] == 1000.0
    assert state_path.exists()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["equity"] == 1000.0
    assert payload["paper"] is True
