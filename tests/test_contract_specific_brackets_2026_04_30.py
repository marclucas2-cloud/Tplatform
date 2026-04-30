from pathlib import Path
from types import SimpleNamespace

from core.worker.cycles.futures_runner import (
    _contract_identity_key,
    _recalculate_bracket_from_fill,
)


def test_contract_identity_key_prefers_local_symbol():
    contract = SimpleNamespace(
        symbol="MCL",
        localSymbol="MCLM6",
        lastTradeDateOrContractMonth="20260518",
    )

    assert _contract_identity_key(contract) == "MCLM6"


def test_contract_identity_key_falls_back_to_symbol_and_month():
    contract = SimpleNamespace(
        symbol="MGC",
        localSymbol=None,
        lastTradeDateOrContractMonth="20260626",
    )

    assert _contract_identity_key(contract) == "MGC:20260626"


def test_futures_runner_contract_specific_bracket_source_guards():
    text = Path("core/worker/cycles/futures_runner.py").read_text(encoding="utf-8")
    assert "reqAllOpenOrders()" in text
    assert 'pos_info.get("local_symbol")' in text
    assert "STALE BRACKET LEG" in text


def test_worker_watchdog_contract_specific_bracket_source_guards():
    text = Path("worker.py").read_text(encoding="utf-8")
    assert "_key_has_stp" in text
    assert "cancelling stale %s %s orderId=%s" in text
    assert 'getattr(pos.contract, "localSymbol", None)' in text


def test_recalculate_bracket_from_fill_keeps_percent_intent():
    sig = SimpleNamespace(side="BUY", stop_loss=104.40, take_profit=112.83)
    sl, tp = _recalculate_bracket_from_fill(
        sig,
        fill_price=105.36,
        signal_price=106.53,
        strategy_params={"sl_pct": 0.02, "tp_pct": 0.04},
    )
    assert sl == 103.25
    assert tp == 109.57


def test_recalculate_bracket_from_fill_keeps_point_intent():
    sig = SimpleNamespace(side="BUY", stop_loss=6980.0, take_profit=7055.0)
    sl, tp = _recalculate_bracket_from_fill(
        sig,
        fill_price=7008.25,
        signal_price=7005.0,
        strategy_params={"sl_points": 25.0, "tp_points": 50.0},
    )
    assert sl == 6983.25
    assert tp == 7058.25
